package org.icrar.awsChiles02.copyS3;

import com.amazonaws.auth.BasicAWSCredentials;
import com.amazonaws.auth.profile.ProfileCredentialsProvider;
import com.amazonaws.services.s3.transfer.TransferManager;

/**
 * Common code for the copying
 */
abstract class AbstractCopyS3 {
  /**
   * Gets bucket name.
   *
   * @param s3String the s 3 string
   * @return the bucket name
   */
  protected String getBucketName(String s3String) {
    if (s3String.startsWith("s3://")) {
      int index = s3String.indexOf('/', 5);
      return s3String.substring(5, index);
    }
    return null;
  }

  /**
   * Gets key name.
   *
   * @param s3String the s 3 string
   * @return the key name
   */
  protected String getKeyName(String s3String) {
    if (s3String.startsWith("s3://")) {
      int index = s3String.indexOf('/', 5);
      return s3String.substring(index + 1);
    }
    return null;
  }

  /**
   * Gets transfer manager.
   *
   * @param profileName     the profile name
   * @param accessKeyId     the access key id
   * @param secretAccessKey the secret access key
   * @return the transfer manager
   */
  protected TransferManager getTransferManager(String profileName, String accessKeyId, String secretAccessKey) {
    TransferManager transferManager;
    if (accessKeyId != null && secretAccessKey != null) {
      BasicAWSCredentials awsCredentials = new BasicAWSCredentials(accessKeyId, secretAccessKey);
      transferManager = new TransferManager(awsCredentials);
    }
    else {
      ProfileCredentialsProvider credentialsProvider =
          (profileName == null)
          ? new ProfileCredentialsProvider()
          : new ProfileCredentialsProvider(profileName);
      transferManager = new TransferManager(credentialsProvider);
    }
    return transferManager;
  }
}